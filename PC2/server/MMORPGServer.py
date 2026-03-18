import json
import math
import os
import socket
import time
import uuid
import asyncio
import struct
import ssl
import psutil
import random
import traceback
from aioquic.asyncio import serve, QuicConnectionProtocol, connect
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import HandshakeCompleted, StreamDataReceived, ConnectionTerminated

# ===========================
# GLOBALS
# ===========================
MAP_PATH = "new_map.txt"

SERVER_TICK = 1/60

CONNECTED_CLIENTS = set()

ENEMIES = []

ENEMY_BULLETS = []
ENEMY_BULLET_SPAWN = 18
ENEMY_BULLET_DESPAWN = 19
ENEMY_BULLET_SPEED = 320.0
ENEMY_BULLET_DAMAGE = 8.0
ENEMY_BULLET_LIFETIME = 1.6

SPEED = 180
SPRINT_SPEED = 360
CROUCH_SPEED = 60

UP = 1 << 0
LEFT = 1 << 1
DOWN = 1 << 2
RIGHT = 1 << 3
SHOOT = 1 << 4
SPRINT = 1 << 5
CROUCH = 1 << 6

DIR_MASK = UP | LEFT | DOWN | RIGHT | SHOOT

MAP_WIDTH = 1920 * 40 # 76800 pixels
MAP_HEIGHT = 1080 * 40 # 43200 pixels
MAP_HALF_WIDTH  = MAP_WIDTH // 2
MAP_HALF_HEIGHT = MAP_HEIGHT // 2

PLAYER_WIDTH = 37
PLAYER_HEIGHT = 56

TILE_SIZE =40
TILE_DEFS = {
    '#': {'walkable': True,  'damages': True},
    '.': {'walkable': True,  'damages': False},
    'T': {'walkable': False, 'damages': False},

    '←': {'walkable': True, 'damages': False},
    '→': {'walkable': True, 'damages': False},
    '↑': {'walkable': True, 'damages': False},
    '↓': {'walkable': True, 'damages': False},

    '↖': {'walkable': True, 'damages': False},
    '↗': {'walkable': True, 'damages': False},
    '↘': {'walkable': True, 'damages': False},
    '↙': {'walkable': True, 'damages': False},

    '⇦': {'walkable': True, 'damages': False},
    '⇨': {'walkable': True, 'damages': False},
    '⇧': {'walkable': True, 'damages': False},
    '⇩': {'walkable': True, 'damages': False},
}
TILE_DICT = {}

LAVA_DAMAGE = 2.5
LAVA_INTERVAL = 0.5

SEQ_BITS = 16
SEQ_MAX = 1 << SEQ_BITS
SEQ_HALF = SEQ_MAX >> 1

MANAGER_STREAM_ID = None
MANAGER_CLIENT = None
MANAGER_IP = None
MANAGER_PORT = None
MANAGER_HOST = None

SERVER_SHORT_ID = f"SRV-{uuid.uuid4().hex[:6]}"

CURRENT_PROCESS = psutil.Process(os.getpid())
CURRENT_PROCESS.cpu_percent() # Call it once to initialize the baseline
CPU_USAGE = 0.0

WAITING_ROOM = {}

# ===========================
# MESH & DYNAMIC CHUNK GLOBALS
# ===========================
SHADOW_PLAYERS = {}

PROJECTILES = []
BULLET_SPEED = 500.0
BULLET_LIFETIME = 2.0

BULLET_SPAWN = 12
BULLET_DESPAWN = 13
HEALING = 16
MSG_WEAPON_UPDATE = 17
MSG_INVENTORY_STATE = 20

BACKEND_PORT = int(os.environ.get("MESH_PORT", 37026))

# The QUIC port for clients (If running multiple servers locally, change this!)
QUIC_PORT = 4433

MY_CHUNKS = []
CHUNK_WIDTH = 1920
CHUNK_HEIGHT = 1080
PEER_CONNECTIONS = {}  # { "SRV-ID": writer_object }
PEER_INFO = {}  # { "SRV-ID": {"quic_ip": "...", "quic_port": ..., "chunks": [[x,y], ...]} }

BACKGROUND_TASKS = set()

ENEMIES_SPAWNED = False

class Weapon:
    def __init__(self, wid, name, kind, cooldown, damage, range1, bullet_speed = 0.0, bullet_lifetime = 0.0):
        self.wid = wid
        self.name = name
        self.kind = kind
        self.cooldown = cooldown
        self.damage = damage
        self.range = range1
        self.last_time_use = 0
        self.bullet_speed = bullet_speed
        self.bullet_lifetime = bullet_lifetime

    def set_damage(self, damage):
        self.damage = damage

WEAPON_PISTOL = 1
WEAPON_KNIFE = 2
WEAPON_BOW=3
HEAL=4
HAND = 9
STRENGTH = 5
SHIELD = 6

PISTOL = Weapon(wid=WEAPON_PISTOL, name="Pistol", kind="ranged", cooldown=0.25, damage=10.0, range1=0.0, bullet_speed=BULLET_SPEED, bullet_lifetime=BULLET_LIFETIME,)
KNIFE = Weapon(wid=WEAPON_KNIFE, name="Knife", kind="melee", cooldown=0.55, damage=25.0, range1=70.0,)
BOW = Weapon(wid=WEAPON_BOW, name="Bow", kind="ranged", cooldown=1.0, damage=20, range1=0.0, bullet_speed=BULLET_SPEED, bullet_lifetime=BULLET_LIFETIME,)
HANDS = Weapon(wid=HAND, name="hands", kind="none", cooldown=0.25, damage=0, range1=70.0,)
HEALS = Weapon(wid=HEAL, name="heal", kind="spell", cooldown=2, damage=0, range1=70.0,)
STRONG = Weapon(wid=STRENGTH, name="strength", kind="spell", cooldown=2, damage=0.0, range1=70.0,)
PROTECT = Weapon(wid=SHIELD, name="shield", kind="spell", cooldown=5, damage=0.0, range1=70.0,)
WEAPONS_BY_ID = {WEAPON_PISTOL: PISTOL, WEAPON_KNIFE: KNIFE, WEAPON_BOW:BOW, HAND: HANDS, HEAL: HEALS, STRENGTH: STRONG, SHIELD: PROTECT}
MSG_SWITCH_WEAPON = 14
MSG_HEAL = 15
MSG_STRENGTH = 16
MSG_TOGGLE_BOT = 21
DROPS = [BOW, HEALS, STRONG, PROTECT]

close_player = False

# ===========================
# QUIC MANAGER SERVER
# ===========================
def get_slot_for_weapon_id(wid):
    if wid == WEAPON_BOW:
        return 2
    if wid == HEAL:
        return 3
    if wid == STRENGTH:
        return 4
    if wid == SHIELD:
        return 5
    return None

def normalize_chunk(chunk):
    return (int(chunk[0]), int(chunk[1]))


def get_inner_spawn_chunks(border_margin_chunks=1):
    if not MY_CHUNKS:
        return []

    owned = [normalize_chunk(c) for c in MY_CHUNKS]

    xs = [c[0] for c in owned]
    ys = [c[1] for c in owned]

    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)

    inner = []
    for cx, cy in owned:
        dist_left = (cx - min_x) // CHUNK_WIDTH
        dist_right = (max_x - cx) // CHUNK_WIDTH
        dist_top = (cy - min_y) // CHUNK_HEIGHT
        dist_bottom = (max_y - cy) // CHUNK_HEIGHT

        if min(dist_left, dist_right, dist_top, dist_bottom) >= border_margin_chunks:
            inner.append((cx, cy))

    return inner


def get_spawn_chunk(border_margin_chunks=1):
    inner = get_inner_spawn_chunks(border_margin_chunks)
    if inner:
        return random.choice(inner)

    if MY_CHUNKS:
        return random.choice([normalize_chunk(c) for c in MY_CHUNKS])

    return None


def get_random_point_in_chunk(chunk_x, chunk_y, padding=80):
    min_x = chunk_x + padding
    max_x = chunk_x + CHUNK_WIDTH - PLAYER_WIDTH - padding

    min_y = chunk_y + padding
    max_y = chunk_y + CHUNK_HEIGHT - PLAYER_HEIGHT - padding

    if min_x > max_x:
        min_x = chunk_x
        max_x = chunk_x + CHUNK_WIDTH - PLAYER_WIDTH

    if min_y > max_y:
        min_y = chunk_y
        max_y = chunk_y + CHUNK_HEIGHT - PLAYER_HEIGHT

    x = random.randint(int(min_x), int(max_x))
    y = random.randint(int(min_y), int(max_y))
    return x, y

class EnemyBullet:
    def __init__(self, bullet_id, enemy_id, x, y, vx, vy, ttl):
        self.bullet_id = bullet_id
        self.enemy_id = enemy_id
        self.x = float(x)
        self.y = float(y)
        self.vx = float(vx)
        self.vy = float(vy)
        self.ttl = float(ttl)
        self.alive = True

    def update(self):
        if not self.alive:
            return

        self.x += self.vx * SERVER_TICK
        self.y += self.vy * SERVER_TICK
        self.ttl -= SERVER_TICK

        if self.ttl <= 0:
            self.alive = False
            return

        if self.x < -MAP_HALF_WIDTH or self.x > MAP_HALF_WIDTH:
            self.alive = False
            return

        if self.y < -MAP_HALF_HEIGHT or self.y > MAP_HALF_HEIGHT:
            self.alive = False
            return

        for player in list(CONNECTED_CLIENTS):
            if player.x is None or player.y is None:
                continue

            if abs(player.x - self.x) < PLAYER_WIDTH and abs(player.y - self.y) < PLAYER_HEIGHT:
                self.alive = False
                self.broadcast_despawn()

                if player.shield_active:
                    return

                player.damage_seq = (player.damage_seq + 1) & 0xFFFF
                player.hp -= ENEMY_BULLET_DAMAGE

                if player.hp <= 0:
                    player.respawn()
                else:
                    player.send_hp_update()
                    player.broadcast_hp_update()

                return

    def broadcast_spawn(self):
        payload = struct.pack(
            "!BIfffff",
            ENEMY_BULLET_SPAWN,
            int(self.bullet_id),
            float(self.x),
            float(self.y),
            float(self.vx),
            float(self.vy),
            float(self.ttl)
        )
        packet = struct.pack("!H", len(payload)) + payload

        for client in list(CONNECTED_CLIENTS):
            try:
                client._quic.send_stream_data(client.state_stream_id, packet, end_stream=False)
            except:
                pass

    def broadcast_despawn(self):
        payload = struct.pack("!BI", ENEMY_BULLET_DESPAWN, int(self.bullet_id))
        packet = struct.pack("!H", len(payload)) + payload

        for client in list(CONNECTED_CLIENTS):
            try:
                client._quic.send_stream_data(client.state_stream_id, packet, end_stream=False)
            except:
                pass

class Enemy:
    def __init__(self, enemy_id):
        self.enemy_id = enemy_id
        self.enemy_type = 0 if enemy_id <= 13 else 1
        self.x = 0
        self.y = 0
        self.hp = 30
        self.current_direction = 0
        self.steps_remaining = 0
        self.close = False
        self.next_move_time = 0.0
        self.next_attack_time = 0.0

    def pick_new_move(self):
        x = int(time.time() * 1000) + self.enemy_id

        x ^= (x << 13) & 0xFFFFFFFF
        x ^= (x >> 17)
        x ^= (x << 5) & 0xFFFFFFFF

        self.current_direction = (x >> 16) % 4
        self.steps_remaining = 20

    def get_closest_player(self):
        closest_player = None
        closest_dist2 = 200 * 200

        for player in list(CONNECTED_CLIENTS):
            dx = player.x - self.x
            dy = player.y - self.y
            dist2 = dx * dx + dy * dy

            if dist2 <= closest_dist2:
                closest_dist2 = dist2
                closest_player = player

        if closest_player is not None:
            return True, closest_player, closest_dist2

        return False, None, closest_dist2

    def update(self):
        now = time.monotonic()
        close, closest_player, distance = self.get_closest_player()

        if not close:
            if self.steps_remaining <= 0:
                if now < self.next_move_time:
                    return

                self.pick_new_move()
                self.next_move_time = now + 0.8

            speed = 100 * SERVER_TICK

            if self.current_direction == 0:
                self.x -= speed
            elif self.current_direction == 1:
                self.x += speed
            elif self.current_direction == 2:
                self.y -= speed
            elif self.current_direction == 3:
                self.y += speed

            self.steps_remaining -= 1
        else:
            self.close=True

            if closest_player:
                dx = closest_player.x - self.x
                dy = closest_player.y - self.y

                distance = math.sqrt(dx * dx + dy * dy)

                if distance != 0:
                    dx /= distance
                    dy /= distance


                if self.enemy_type == 0:
                    # ===== MELEE =====
                    speed = 200 * SERVER_TICK
                    self.x += dx * speed
                    self.y += dy * speed

                    if distance < 40 and now >= self.next_attack_time:
                        self.next_attack_time = now + 0.5

                        if closest_player.shield_active:
                            return

                        closest_player.damage_seq = (closest_player.damage_seq + 1) & 0xFFFF
                        closest_player.hp -= 6

                        if closest_player.hp <= 0:
                            closest_player.respawn()
                        else:
                            closest_player.send_hp_update()
                            closest_player.broadcast_hp_update()

                else:
                    # ===== RANGED =====
                    if distance > 250:
                        speed = 150 * SERVER_TICK
                        self.x += dx * speed
                        self.y += dy * speed
                    else:
                        self.shoot(closest_player)

    def shoot(self, player):
        now = time.monotonic()
        if now < self.next_attack_time:
            return

        dx = player.x - self.x
        dy = player.y - self.y
        dist = math.hypot(dx, dy)

        if dist == 0:
            return

        dx /= dist
        dy /= dist

        bullet_id = random.randint(1, 2_000_000_000)

        bullet = EnemyBullet(
            bullet_id=bullet_id,
            enemy_id=self.enemy_id,
            x=self.x + PLAYER_WIDTH / 2,
            y=self.y + PLAYER_HEIGHT / 2,
            vx=dx * ENEMY_BULLET_SPEED,
            vy=dy * ENEMY_BULLET_SPEED,
            ttl=ENEMY_BULLET_LIFETIME
        )

        ENEMY_BULLETS.append(bullet)
        bullet.broadcast_spawn()

        self.next_attack_time = now + 1.0

    def broadcast_enemy(self):
        payload = struct.pack(
            "!BffffI",
            11,
            self.enemy_id,
            self.x,
            self.y,
            self.hp,
            self.current_direction
        )

        packet = struct.pack("!H", len(payload)) + payload

        for client in list(CONNECTED_CLIENTS):
            try:
                client._quic.send_stream_data(client.state_stream_id, packet, end_stream=False)
            except:
                pass

    def respawn_enemy(self):
        if not MY_CHUNKS:
            # אם השרת עוד לא קיבל צ'אנקים מהמנג'ר, נשים את האויב רחוק בינתיים
            self.x, self.y = 999999, 999999
            return

        # 1. מציאת הגבולות המקסימליים של האזור שהשרת הזה אחראי עליו
        min_x = min(c[0] for c in MY_CHUNKS)
        max_x = max(c[0] for c in MY_CHUNKS) + CHUNK_WIDTH
        min_y = min(c[1] for c in MY_CHUNKS)
        max_y = max(c[1] for c in MY_CHUNKS) + CHUNK_HEIGHT

        # 2. הגדרת ה-Padding שביקשת (8 צ'אנקים מהצדדים, 38 מלמעלה/למטה למשל)
        # הערה: אם התכוונת לפיקסלים, השאר את המספרים ככה.
        # אם התכוונת למרחק גדול יותר, שנה את הערכים האלו.
        pad_x = 80
        pad_y = 80

        # 3. הגרלת מיקום על פני כל שטח השרת עם הריפוד מהגבולות
        self.x = random.uniform(min_x + pad_x, max_x - pad_x)
        self.y = random.uniform(min_y + pad_y, max_y - pad_y)

        # אתחול שאר נתוני האויב
        self.hp = 30
        self.current_direction = 0
        self.steps_remaining = 0
        self.close = False
        self.next_move_time = 0.0
        self.next_attack_time = 0.0

        self.broadcast_enemy()

# ===========================
# LOAD BALANCING
# ===========================

async def load_balancer_loop():
    """Monitors CPU. If overloaded, forces an idle server to take a chunk."""
    while True:
        await asyncio.sleep(5)  # Check load every 5 seconds

        # If we are above 85% CPU, and we have chunks to spare
        if CPU_USAGE > 85.0 and len(MY_CHUNKS) > 1:
            best_peer = None
            lowest_load = 100.0

            # Find a server with less than 50% CPU load
            for pid, info in PEER_INFO.items():
                peer_load = info.get("load", 100.0)
                if peer_load < 50.0 and peer_load < lowest_load:
                    lowest_load = peer_load
                    best_peer = pid

            if best_peer:
                # Shed our most recently acquired chunk
                chunk_to_shed = MY_CHUNKS[-1]
                print(f"[LOAD BALANCER] CPU at {CPU_USAGE}%. Offering chunk {chunk_to_shed} to {best_peer}.")

                payload = json.dumps({
                    "type": "OFFER_CHUNK",
                    "chunk": chunk_to_shed
                }).encode()
                packet = struct.pack("!I", len(payload)) + payload

                writer = PEER_CONNECTIONS.get(best_peer)
                if writer:
                    if not writer.is_closing():
                        try:
                            writer.write(packet)
                        except:
                            pass

# ===========================
# BACKEND MESH (TCP Server-to-Server)
# ===========================

def create_bg_task(coro):
    """Safely wraps an asyncio task so the Garbage Collector doesn't kill it."""
    task = asyncio.create_task(coro)
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)
    return task


def send_transfer_server_to_client(msg):
    try:
        pid = msg["pid"]
        ip = msg["ip"]
        port = msg["port"]
        token = msg["token"]

        pid_bytes = uuid.UUID(hex=pid).bytes
        token_bytes = uuid.UUID(str(token)).bytes
        ip_bytes = socket.inet_aton(str(ip))

        payload = struct.pack(
            "!B16s4si16s",
            10,
            pid_bytes,
            ip_bytes,
            port,
            token_bytes
        )

        packet = struct.pack("!H", len(payload)) + payload

        for client in list(CONNECTED_CLIENTS):
            if client.client_id and client.client_id.hex == pid:
                try:
                    client._quic.send_stream_data(client.state_stream_id, packet, end_stream=True)
                except: pass

                # [FIXED] Clean, async-safe delayed close
                async def delayed_close(c):
                    await asyncio.sleep(1.0)
                    try:
                        c._quic.close()
                    except: pass

                create_bg_task(delayed_close(client))
                break
    except Exception as e:
        print(f"[HANDOFF] Error sending jump packet: {e}")


def request_handoff_from_mesh(client, target_peer):
    inv = client.get_inventory_save_data()
    req = {
        "type": "REQUEST_HANDOFF",
        "pid": client.client_id.hex,
        "user": client.user_name,
        "x": client.x,
        "y": client.y,
        "hp": client.hp,
        "intent": getattr(client, 'current_intent', 0),
        "seq": getattr(client, 'last_seq', 0),
        "bow": inv["bow"],
        "heal": inv["heal"],
        "strength": inv["strength"],
        "shield": inv["shield"],
        "active_weapon_id": inv["active_weapon_id"],
    }
    payload = json.dumps(req).encode()
    packet = struct.pack("!I", len(payload)) + payload

    writer = PEER_CONNECTIONS.get(target_peer)
    # Check if pipe is broken BEFORE trying to write
    if not writer or writer.is_closing():
        print(f"[MESH] Cannot route to {target_peer}, connection is closed.")
        PEER_CONNECTIONS.pop(target_peer, None)
        client.handoff_in_progress = False  # <--- RESCUE THE CLIENT
        return

    try:
        writer.write(packet)
        create_bg_task(writer.drain())
    except Exception as e:
        print(f"[MESH] Failed to send handoff to {target_peer}: {e}")
        PEER_CONNECTIONS.pop(target_peer, None)
        client.handoff_in_progress = False


def yield_stolen_chunks(peer_id, peer_chunks):
    global MY_CHUNKS
    stolen_chunks = [c for c in MY_CHUNKS if c in peer_chunks]
    if not stolen_chunks:
        return

    print(f"[MAP] Yielding chunks {stolen_chunks} back to rightful owner {peer_id}")
    MY_CHUNKS = [c for c in MY_CHUNKS if c not in stolen_chunks]

    for client in list(CONNECTED_CLIENTS):
        if not getattr(client, 'authenticated', False): continue

        cx = (int(client.x) // CHUNK_WIDTH) * CHUNK_WIDTH
        cy = (int(client.y) // CHUNK_HEIGHT) * CHUNK_HEIGHT

        if [cx, cy] in stolen_chunks:
            print(f"[HANDOFF] Transferring {client.user_name} to rightful owner {peer_id}")
            client.authenticated = False
            request_handoff_from_mesh(client, peer_id)


async def process_mesh_messages(reader, writer, peer_addr):
    peer_id_connected = None
    try:
        while True:
            try:
                length_bytes = await reader.readexactly(4)
            except asyncio.IncompleteReadError:
                break # Normal mesh disconnect, stop listening

            if not length_bytes: break

            length = struct.unpack("!I", length_bytes)[0]

            try:
                data = await reader.readexactly(length)
            except asyncio.IncompleteReadError:
                break # Normal mesh disconnect

            try:
                msg = json.loads(data.decode())

                if msg["type"] == "MESH_HELLO":
                    peer_id_connected = msg["server_id"]
                    PEER_CONNECTIONS[peer_id_connected] = writer

                    peer_chunks = msg.get("chunks", [])
                    PEER_INFO[peer_id_connected] = {
                        "quic_ip": msg.get("quic_ip", "127.0.0.1"),
                        "quic_port": msg.get("quic_port", 4433),
                        "chunks": peer_chunks
                    }
                    print(f"[MESH] Active peer {peer_id_connected} joined. Total peers: {len(PEER_CONNECTIONS)}")
                    yield_stolen_chunks(peer_id_connected, peer_chunks)

                elif msg["type"] == "CHUNK_UPDATE":
                    p_id = msg["server_id"]
                    if p_id in PEER_INFO:
                        peer_chunks = msg["chunks"]
                        PEER_INFO[p_id]["chunks"] = peer_chunks
                        yield_stolen_chunks(p_id, peer_chunks)


                elif msg["type"] == "GHOST_SYNC":
                    if peer_id_connected in PEER_INFO:
                        PEER_INFO[peer_id_connected]["load"] = msg.get("load", 0)

                    for ghost in msg["ghosts"]:
                        pid = uuid.UUID(hex=ghost["pid"])
                        SHADOW_PLAYERS[pid] = {
                            "x": ghost["x"],
                            "y": ghost["y"],
                            "hp": ghost["hp"],
                            "dir": ghost["dir"],
                            "last_updated": time.monotonic()
                        }

                        payload = struct.pack("!B16sfffB", 1, pid.bytes, ghost["x"], ghost["y"], ghost["hp"], ghost["dir"])
                        packet = struct.pack("!H", len(payload)) + payload

                        for client in list(CONNECTED_CLIENTS):
                            if getattr(client, 'authenticated', False):
                                try:
                                    client._quic.send_stream_data(client.state_stream_id, packet, end_stream=False)
                                except: pass

                elif msg["type"] == "OFFER_CHUNK":
                    chunk = msg["chunk"]
                    if chunk not in MY_CHUNKS:
                        print(f"[LOAD BALANCER] Helping overloaded peer! Taking chunk {chunk}.")
                        MY_CHUNKS.append(chunk)

                        # Tell everyone we own it now. (This triggers the overloaded server to yield it!)
                        update_msg = json.dumps({
                            "type": "CHUNK_UPDATE",
                            "server_id": SERVER_SHORT_ID,
                            "chunks": MY_CHUNKS
                        }).encode()
                        packet = struct.pack("!I", len(update_msg)) + update_msg

                        for peer_id, w in list(PEER_CONNECTIONS.items()):
                            if not w.is_closing():
                                try:
                                    w.write(packet)
                                except:
                                    pass

                        if MANAGER_CLIENT:
                            try:
                                stream_id = MANAGER_CLIENT._quic.get_next_available_stream_id(False)
                                MANAGER_CLIENT._quic.send_stream_data(stream_id, update_msg, end_stream=False)
                                MANAGER_CLIENT.transmit()
                            except:
                                pass

                elif msg["type"] == "REQUEST_HANDOFF":
                    cx = (int(msg["x"]) // CHUNK_WIDTH) * CHUNK_WIDTH
                    cy = (int(msg["y"]) // CHUNK_HEIGHT) * CHUNK_HEIGHT

                    if [cx, cy] in MY_CHUNKS:
                        token = uuid.uuid4().hex
                        WAITING_ROOM[token] = {
                            "pid": uuid.UUID(hex=msg["pid"]),
                            "user": msg["user"],
                            "x": msg["x"],
                            "y": msg["y"],
                            "hp": msg["hp"],
                            "intent": msg.get("intent", 0),
                            "seq": msg.get("seq", 0),
                            "bow": msg.get("bow", 0),
                            "heal": msg.get("heal", 0),
                            "strength": msg.get("strength", 0),
                            "shield": msg.get("shield", 0),
                            "active_weapon_id": msg.get("active_weapon_id", 9),
                        }
                        reply = {
                            "type": "HANDOFF_ACCEPTED",
                            "pid": msg["pid"],
                            "token": token,
                            "ip": os.environ.get("PUBLIC_IP", "127.0.0.1"),
                            "port": QUIC_PORT
                        }
                        payload = json.dumps(reply).encode()
                        if not writer.is_closing():
                            try:
                                writer.write(struct.pack("!I", len(payload)) + payload)
                                await writer.drain()
                            except:
                                print("[MESH] Failed to write to server")

                elif msg["type"] == "HANDOFF_ACCEPTED":
                    send_transfer_server_to_client(msg)

            except Exception as e:
                # IF THE MESSAGE CRASHES, WE CATCH IT HERE!
                print(f"[MESH] Error processing specific message: {e}")
                traceback.print_exc()

                # [CRITICAL] Do not break! Move on to the next message!
                continue

    except Exception as e:
        print(f"\n[FATAL MESH ERROR] Peer connection error: {e}")
        traceback.print_exc()
        print("-" * 40 + "\n")
    finally:
        writer.close()
        try:
            await writer.wait_closed()  # [FIX] Gracefully close connection
        except: pass

        if peer_id_connected:
            PEER_CONNECTIONS.pop(peer_id_connected, None)
            PEER_INFO.pop(peer_id_connected, None)
            print(f"[MESH] Peer {peer_id_connected} left. Total peers: {len(PEER_CONNECTIONS)}")


async def handle_server_peer(reader, writer):
    peer_addr = writer.get_extra_info('peername')
    await process_mesh_messages(reader, writer, peer_addr)


async def start_backend_mesh():
    server = await asyncio.start_server(handle_server_peer, '0.0.0.0', BACKEND_PORT)
    print(f"[MESH] Listening for peer servers on TCP {BACKEND_PORT}")
    async with server:
        await server.serve_forever()


async def connect_to_peer(peer_id, ip, port):
    if peer_id in PEER_CONNECTIONS: return
    try:
        reader, writer = await asyncio.open_connection(ip, int(port))

        hello_msg = {
            "type": "MESH_HELLO",
            "server_id": SERVER_SHORT_ID,
            "quic_ip": os.environ.get("PUBLIC_IP", "127.0.0.1"),
            "quic_port": QUIC_PORT,
            "chunks": MY_CHUNKS
        }
        payload = json.dumps(hello_msg).encode()
        writer.write(struct.pack("!I", len(payload)) + payload)
        await writer.drain()

        create_bg_task(process_mesh_messages(reader, writer, f"{ip}:{port}"))
    except Exception as e:
        print(f"[MESH] Failed to connect to {peer_id}: {e}")


async def server_to_server_sync():
    while True:
        await asyncio.sleep(SERVER_TICK * 5)
        if not PEER_CONNECTIONS: continue

        ghosts = []
        for client in list(CONNECTED_CLIENTS):
            if getattr(client, 'authenticated', False) and client.client_id:
                ghosts.append({
                    "pid": client.client_id.hex,
                    "x": client.x, "y": client.y, "hp": client.hp, "dir": client.dir
                })

        # [REPLACED] We send GHOST_SYNC even if empty, just to transmit our CPU load!
        payload = json.dumps({
            "type": "GHOST_SYNC",
            "ghosts": ghosts,
            "load": CPU_USAGE  # <-- Sharing our true load!
        }).encode()

        packet = struct.pack("!I", len(payload)) + payload

        # Safely broadcast ghost data to peers
        for peer_id, w in list(PEER_CONNECTIONS.items()):
            if w.is_closing():
                PEER_CONNECTIONS.pop(peer_id, None)
                PEER_INFO.pop(peer_id, None)
                continue

            try:
                w.write(packet)
                create_bg_task(w.drain())
            except Exception:
                # If write fails, clean up the dead connection
                PEER_CONNECTIONS.pop(peer_id, None)
                PEER_INFO.pop(peer_id, None)
                try:
                    w.close()
                except:
                    pass


async def cleanup_shadows():
    while True:
        await asyncio.sleep(1)
        now = time.monotonic()
        for pid in list(SHADOW_PLAYERS.keys()):
            if now - SHADOW_PLAYERS[pid].get("last_updated", 0) > 2.0:
                SHADOW_PLAYERS.pop(pid, None)

                # Broadcast ghost disconnect to local clients
                payload = struct.pack("!B16s", 3, pid.bytes)
                packet = struct.pack("!H", len(payload)) + payload

                for client in list(CONNECTED_CLIENTS):
                    if getattr(client, 'authenticated', False):
                        try:
                            client._quic.send_stream_data(client.state_stream_id, packet, end_stream=False)
                        except:
                            pass


# ===========================
# QUIC MANAGER SERVER
# ===========================



class ManagerClientProtocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_ping = time.monotonic()
        self.recv_buffer = bytearray()  # [FIXED] Stream Buffer

    def quic_event_received(self, event):
        if isinstance(event, StreamDataReceived):
            self.recv_buffer.extend(event.data)

            # [FIXED] Only process complete JSON strings
            while b'\n' in self.recv_buffer:
                line, self.recv_buffer = self.recv_buffer.split(b'\n', 1)
                if not line: continue

                try:
                    msg = json.loads(line.decode())
                    if msg["type"] == "EXPECTED_PLAYER":
                        token = msg["token"]
                        pid = msg["pid"]
                        pid_uuid = uuid.UUID(pid) if isinstance(pid, str) else pid
                        WAITING_ROOM[token] = {
                            "pid": pid_uuid,
                            "user": msg["user"],
                            "x": msg["x"],
                            "y": msg["y"],
                            "hp": msg["hp"],
                            "bow": msg.get("bow", 0),
                            "heal": msg.get("heal", 0),
                            "strength": msg.get("strength", 0),
                            "shield": msg.get("shield", 0),
                            "active_weapon_id": msg.get("active_weapon_id", 9),
                        }
                        print(f"[SERVER] Expecting player {msg['user']} with token {token[:8]}...")

                    elif msg["type"] == "HEARTBEAT":
                        self.last_ping = time.monotonic()
                        self._quic.send_stream_data(event.stream_id, json.dumps({"type": None}).encode() + b'\n', end_stream=True)

                    elif msg["type"] == "CHUNK_ASSIGNMENT":
                        global MY_CHUNKS, ENEMIES_SPAWNED
                        MY_CHUNKS = [list(c) for c in msg["my_chunks"]]
                        print(f"[SERVER] Assigned {len(MY_CHUNKS)} chunks.")

                        if not ENEMIES_SPAWNED and MY_CHUNKS:
                            spawn_enemies()
                            ENEMIES_SPAWNED = True

                        for peer_id, address in msg["directory"].items():
                            ip, port = address.split(":")
                            create_bg_task(connect_to_peer(peer_id, ip, port))

                    elif msg["type"] == "NEW_PEER":
                        ip, port = msg["address"].split(":")
                        create_bg_task(connect_to_peer(msg["peer_id"], ip, port))
                except Exception as e:
                    print(f"[MANAGER] Error parsing stream: {e}")

        if isinstance(event, ConnectionTerminated):
            print("[MANAGER] Connection terminated. Exiting.")
            os._exit(0)

def get_local_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        print(f"[SERVER] Detected local IP: {ip}")
        return ip
    finally:
        sock.close()


async def find_manager():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    sock.bind(("0.0.0.0", 37025))
    sock.setblocking(False)

    loop = asyncio.get_running_loop()
    print("[MANAGER] Searching for manager...")

    while True:
        data, addr = await loop.sock_recvfrom(sock, 1024)
        msg = json.loads(data.decode())

        if msg.get("id") == "MANAGER_QUIC_AUTH":
            manager_ip = msg.get("ip", addr[0])
            print(f"[MANAGER] Found at {manager_ip}:{msg['port']}")

            sock.close()

            return manager_ip, msg["port"], msg["host"]


async def connect_to_manager():
    global MANAGER_CLIENT, MANAGER_STREAM_ID, MANAGER_IP, MANAGER_PORT, MANAGER_HOST

    MANAGER_IP, MANAGER_PORT, MANAGER_HOST = await find_manager()

    config = QuicConfiguration(
        is_client=True,
        alpn_protocols=["manager-proto"],
        idle_timeout = 300.0  # <--- NEW: Keep connection alive for 5 minutes
    )
    config.verify_mode = ssl.CERT_REQUIRED
    config.load_verify_locations("ca.cert.pem")
    config.server_name = MANAGER_HOST

    async with connect(
        MANAGER_IP,
        MANAGER_PORT,
        configuration=config,
        create_protocol=ManagerClientProtocol,
        stream_handler=None  # Optional: skips auto stream handling since we are custom
    ) as client:

        MANAGER_CLIENT = client
        MANAGER_STREAM_ID = client._quic.get_next_available_stream_id(False)

        # ---- SERVER HELLO (ONCE) ----
        public_ip = os.environ.get("PUBLIC_IP")
        if not public_ip:
            public_ip = get_local_ip()

        send_to_manager({
            "type": "SERVER_HELLO",
            "id": SERVER_SHORT_ID,
            "port": 4433,
            "public_ip": public_ip,
            "mesh_port": BACKEND_PORT
        })

        # ---- SNAPSHOT LOOP (HEARTBEAT) ----
        manager_timeout = 20
        count = 0
        max_count = 24
        while True:
            while count < max_count:
                now = time.monotonic()
                if now - client.last_ping > manager_timeout:
                    print(f"[MANAGER] Timed out")
                    os._exit(0)

                await asyncio.sleep(5)

                count += 1
            count = 0
            send_server_snapshot()


def send_to_manager(payload: dict):
    if not MANAGER_CLIENT: return
    MANAGER_CLIENT._quic.send_stream_data(MANAGER_STREAM_ID, json.dumps(payload).encode() + b'\n', end_stream=False)
    MANAGER_CLIENT.transmit()


def send_server_snapshot():
    players = []

    for c in list(CONNECTED_CLIENTS):
        if c.client_id:
            players.append({
                "player_id": c.client_id.hex,
                "x": c.x,
                "y": c.y,
                "hp": c.hp
            })


    send_to_manager({
        "type": "SERVER_SNAPSHOT",
        "server_id": SERVER_SHORT_ID,
        "players": players,
        "load": CPU_USAGE
    })

class Bullet:
    def __init__(self, x, y, dir_x, dir_y):
        self.x = x
        self.y = y
        self.dir_x = dir_x
        self.dir_y = dir_y
        self.lifetime = 1.0

    def move_bullet(self):
        self.x += self.dir_x
        self.y += self.dir_y




# ===========================
# QUIC GAME SERVER
# ===========================


class GameServerProtocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dir = 0
        # 0 = down
        # 1 = up
        # 2 = left
        # 3 = right
        self.authenticated = False
        self.was_moving = False

        self.x = None
        self.y = None
        self.hp = None

        self.bot_mode = False
        self.bot_direction = 0
        self.bot_steps = 0
        self.bot_next_move_time = 0.0

        self.bullet_active = False
        self.bullet_x = 0.0
        self.bullet_y = 0.0
        self.bullet_vx = 0.0
        self.bullet_vy = 0.0
        self.bullet_ttl = 0.0
        self.shoot_pending = False
        self.last_dir_x = 1.0
        self.last_dir_y = 0.0

        self.inventory = [
            WEAPONS_BY_ID[WEAPON_PISTOL],  # slot 0
            WEAPONS_BY_ID[WEAPON_KNIFE],  # slot 1
            None,  # slot 2 = bow
            None,  # slot 3 = heal
            None,  # slot 4 = strength
            None,  # slot 5 = shield
            WEAPONS_BY_ID[HAND]  # slot 6 = hands
        ]

        self.active_weapon = self.inventory[6]
        self.active_weapon_id = self.active_weapon.wid
        self.last_weapon_use = 0.0

        self.shield_active = False
        self.shield_start_time = 0.0
        self.shield_duration = 5.0

        self.strength_active = False
        self.strength_start_time = 0.0
        self.strength_duration = 5.0

        self.client_id: uuid.UUID | None = None
        self.last_seq = 0
        self.damage_seq = 0

        self.control_stream_id = None
        self.state_stream_id = None

        self.recv_buffers = {}

        self.last_heartbeat = time.time()
        self.heartbeat_timeout = 7.0

        self.current_intent = 0
        self.user_name = "Unknown"

        self.packet_count = 0
        self.last_packet_time = time.time()

        self.handoff_in_progress = False
        self.joined_server_time = time.monotonic()

    def pick_new_bot_move(self):
        x = int(time.time() * 1000) + id(self)

        x ^= (x << 13) & 0xFFFFFFFF
        x ^= (x >> 17)
        x ^= (x << 5) & 0xFFFFFFFF

        self.bot_direction = (x >> 16) % 4
        self.bot_steps = 20  # Number of ticks/tiles to move in this direction

    def quic_event_received(self, event): # This is the only function QUIC calls.
        if isinstance(event, HandshakeCompleted):
            print("Unknown client connected, waiting for token...")

        elif isinstance(event, StreamDataReceived):
            if event.stream_id not in self.recv_buffers:
                self.recv_buffers[event.stream_id] = bytearray()

            self.recv_buffers[event.stream_id].extend(event.data)
            self.process_recv_buffer(event.stream_id)

        # [FIXED] Catch the client leaving so they don't become a ghost!
        elif isinstance(event, ConnectionTerminated):
            print(f"QUIC connection closed by client.")
            self.connection_loss()

    async def handle_handshake(self):
        print("Client connected")

        self.control_stream_id = self._quic.get_next_available_stream_id(False)
        self.state_stream_id = self._quic.get_next_available_stream_id(True)

        CONNECTED_CLIENTS.add(self)

        payload = struct.pack("!B16sfff", 0, self.client_id.bytes, self.x, self.y, self.hp)
        packet = struct.pack("!H", len(payload)) + payload
        self._quic.send_stream_data(self.control_stream_id, packet, end_stream=False)
        self.transmit()

        await asyncio.sleep(0.5)

        self.broadcast_new_connection()

        self.broadcast_online_clients()

        self.active_weapon_id = self.active_weapon.wid
        self.broadcast_weapon_update(self.active_weapon_id)

        self.send_inventory_state()

    async def safe_handle_handshake(self):
        try:
            await self.handle_handshake()
        except Exception as e:
            print("handshake failed:", e)
            try:
                self._quic.close()
            except:
                pass

    def process_recv_buffer(self, stream_id):
        buffer = self.recv_buffers[stream_id]

        while True:
            if len(buffer) < 2:
                return

            msg_len = struct.unpack("!H", buffer[:2])[0]

            if len(buffer) < 2 + msg_len:
                return

            payload = buffer[2:2 + msg_len]
            del buffer[:2 + msg_len]

            self.handle_message(payload)


    def handle_message(self, data):
        self.last_heartbeat = time.time()

        if not self.authenticated:
            self.process_login_packet(data)
            return

        now = time.time()
        if now - self.last_packet_time >= 1.0:
            self.packet_count = 0
            self.last_packet_time = now

        self.packet_count += 1
        if self.packet_count > 6000:
            return  # Drop the packet if they exceed 60 per second

        msg_type = data[0]  # We use binary protocol. The first byte is the message type

        if msg_type == 0:
            typee = struct.unpack("!B", data[1:])
            typee = int(typee[0])
            if typee == 0:
                self.connection_loss()

        elif msg_type == 1:
            intent, seq = struct.unpack("!BH", data[1:])

            self.last_seq = seq
            self.current_intent = intent

            if intent & SHOOT:
                self.shoot_pending = True

            self.current_intent = intent & ~SHOOT

        elif msg_type == 2:
            length = struct.unpack("!I", data[1:5])[0]
            if length > 256:
                return
            message = data[5:5 + length]
            self.broadcast_message(message)

        elif msg_type == 5:
            self.last_heartbeat = time.time()

            payload = struct.pack("!B", 6) # msg type 6 = pong
            packet = struct.pack("!H", len(payload)) + payload
            self._quic.send_stream_data(self.control_stream_id, packet, end_stream=False)
            self.transmit()

        elif msg_type == MSG_SWITCH_WEAPON:
            slot_index = struct.unpack("!B", data[1:2])[0]

            if 0 <= slot_index < len(self.inventory):
                self.active_weapon = self.inventory[slot_index]
                self.active_weapon_id = self.active_weapon.wid
                self.broadcast_weapon_update(self.active_weapon_id)

        elif msg_type == MSG_HEAL:
            if self.active_weapon==self.inventory[2]:
                if self.hp<90:
                    self.hp += 10
                else:
                    self.hp = 100
                self.send_hp_update()
                self.broadcast_hp_update()

        elif msg_type == MSG_STRENGTH:
            if self.active_weapon==self.inventory[3]:
                PISTOL.set_damage(15)
                KNIFE.set_damage(30)

        elif msg_type == MSG_TOGGLE_BOT:
            self.bot_mode = not self.bot_mode
            if self.bot_mode:
                self.pick_new_bot_move()
            else:
                self.current_intent = 0

    def process_login_packet(self, data):
        try:
            msg_type = data[0]
            if msg_type != 9:
                print("Invalid first packet")
                self._quic.close()
                return

            token_bytes = data[1:]
            token = token_bytes.decode()
            print(token)

            if token in WAITING_ROOM:
                user_data = WAITING_ROOM.pop(token)

                self.client_id = user_data["pid"]
                self.user_name = user_data["user"]
                self.hp = user_data["hp"]
                self.inventory = [
                    WEAPONS_BY_ID[WEAPON_PISTOL],  # slot 0
                    WEAPONS_BY_ID[WEAPON_KNIFE],  # slot 1
                    WEAPONS_BY_ID[WEAPON_BOW] if user_data.get("bow", 0) else None,
                    WEAPONS_BY_ID[HEAL] if user_data.get("heal", 0) else None,
                    WEAPONS_BY_ID[STRENGTH] if user_data.get("strength", 0) else None,
                    WEAPONS_BY_ID[SHIELD] if user_data.get("shield", 0) else None,
                    WEAPONS_BY_ID[HAND]  # slot 6
                ]

                self.current_intent = user_data.get("intent", 0)
                self.last_seq = user_data.get("seq", 0)

                # Base coordinates
                self.x = float(user_data["x"])
                self.y = float(user_data["y"])

                # ==========================================
                # THE SPATIAL NUDGE
                # Push the player 5 pixels safely over the border
                # based on the direction they were traveling
                # ==========================================
                nudge_amount = 5.0

                if self.current_intent & RIGHT:
                    self.x += nudge_amount
                elif self.current_intent & LEFT:
                    self.x -= nudge_amount

                if self.current_intent & DOWN:
                    self.y += nudge_amount
                elif self.current_intent & UP:
                    self.y -= nudge_amount
                # ==========================================

                saved_weapon_id = user_data.get("active_weapon_id", HAND)

                slot_by_weapon_id = {
                    WEAPON_PISTOL: 0,
                    WEAPON_KNIFE: 1,
                    WEAPON_BOW: 2,
                    HEAL: 3,
                    STRENGTH: 4,
                    SHIELD: 5,
                    HAND: 6
                }

                slot_index = slot_by_weapon_id.get(saved_weapon_id, 6)

                if self.inventory[slot_index] is None:
                    slot_index = 6

                self.active_weapon = self.inventory[slot_index]
                self.active_weapon_id = self.active_weapon.wid

                self.authenticated = True
                print(f"Player {self.user_name} successfully logged in")

                create_bg_task(self.safe_handle_handshake())
            else:
                print("Invalid token")
                self._quic.close()
        except Exception as e:
            print("AUTH ERROR:", e)
            self._quic.close()

    def get_inventory_save_data(self):
        return {
            "bow": 1 if self.inventory[2] is not None else 0,
            "heal": 1 if self.inventory[3] is not None else 0,
            "strength": 1 if self.inventory[4] is not None else 0,
            "shield": 1 if self.inventory[5] is not None else 0,
            "active_weapon_id": self.active_weapon_id
        }

    def _send_framed(self, client, payload: bytes):
        packet = struct.pack("!H", len(payload)) + payload
        try:
            client._quic.send_stream_data(client.state_stream_id, packet, end_stream=False)
        except:
            pass

    def broadcast_bullet_spawn(self, x0, y0, vx, vy, ttl):
        payload = struct.pack(
            "!B16sfffff",
            BULLET_SPAWN,
            self.client_id.bytes,  # מי ירה
            float(x0), float(y0),
            float(vx), float(vy),
            float(ttl),
        )
        for c in list(CONNECTED_CLIENTS):
            self._send_framed(c, payload)

    def broadcast_bullet_despawn(self):
        payload = struct.pack("!B16s", BULLET_DESPAWN, self.client_id.bytes)
        for c in list(CONNECTED_CLIENTS):
            self._send_framed(c, payload)

    def broadcast_weapon_update(self, wid: int):
        payload = struct.pack("!B16sB", MSG_WEAPON_UPDATE, self.client_id.bytes, int(wid))
        for c in list(CONNECTED_CLIENTS):
            self._send_framed(c, payload)

    def send_inventory_state(self):
        payload = struct.pack(
            "!BBBBBBBB",
            20,
            self.inventory[0].wid if self.inventory[0] else 0,
            self.inventory[1].wid if self.inventory[1] else 0,
            self.inventory[2].wid if self.inventory[2] else 0,
            self.inventory[3].wid if self.inventory[3] else 0,
            self.inventory[4].wid if self.inventory[4] else 0,
            self.inventory[5].wid if self.inventory[5] else 0,
            self.inventory[6].wid if self.inventory[6] else 0,
        )

        packet = struct.pack("!H", len(payload)) + payload
        self._quic.send_stream_data(self.state_stream_id, packet, end_stream=False)
        self.transmit()


    # ===========================
    # MOVEMENT & COLLISIONS
    # ===========================
    def give_random_enemy_drop(self):
        drop_weapon = random.choice(DROPS)
        slot_index = get_slot_for_weapon_id(drop_weapon.wid)

        if slot_index is None:
            return

        if self.inventory[slot_index] is None:
            self.inventory[slot_index] = drop_weapon
            self.send_inventory_state()

    def get_weapon_damage(self, w: Weapon):
        damage = w.damage

        if self.strength_active:
            if w.wid == WEAPON_PISTOL:
                damage = 20.0
            elif w.wid == WEAPON_KNIFE:
                damage = 35.0
            elif w.wid == WEAPON_BOW:
                damage = 30.0

        return damage

    def fire_ranged_weapon(self, w: Weapon):
        if self.last_dir_x is None or self.last_dir_y is None:
            return

        fx, fy = self.last_dir_x, self.last_dir_y
        L = math.hypot(fx, fy)
        if L == 0:
            return

        fx /= L
        fy /= L

        self.bullet_active = True
        self.bullet_x = float(self.x)
        self.bullet_y = float(self.y)
        self.bullet_vx = fx * w.bullet_speed
        self.bullet_vy = fy * w.bullet_speed
        self.bullet_ttl = w.bullet_lifetime

        self.broadcast_bullet_spawn(
            self.bullet_x,
            self.bullet_y,
            self.bullet_vx,
            self.bullet_vy,
            self.bullet_ttl,
        )

    def fire_melee_weapon(self, w: Weapon):
        for enemy in list(ENEMIES):
            dx = enemy.x - self.x
            dy = enemy.y - self.y
            dist = math.hypot(dx, dy)

            if dist <= w.range:
                enemy.hp -= self.get_weapon_damage(w)

                if enemy.hp <= 0:
                    enemy.hp = 0
                    self.give_random_enemy_drop()
                    self.give_random_enemy_drop()
                    enemy.respawn_enemy()
                else:
                    enemy.broadcast_enemy()

                break

    def spell(self, w: Weapon):
        global strength_start_time, strength_active

        if w.name == "heal":
            if self.hp < 90:
                self.hp += 10
            else:
                self.hp = 100

            self.send_hp_update()
            self.broadcast_hp_update()

            self.inventory[3] = None
            self.active_weapon = self.inventory[6]
            self.active_weapon_id = self.active_weapon.wid
            self.broadcast_weapon_update(self.active_weapon_id)
            self.send_inventory_state()


        elif w.name == "strength":
            self.strength_active = True
            self.strength_start_time = time.monotonic()
            self.inventory[4] = None
            self.active_weapon = self.inventory[6]
            self.active_weapon_id = self.active_weapon.wid
            self.broadcast_weapon_update(self.active_weapon_id)
            self.send_inventory_state()

        elif w.name == "shield":
            self.shield_active = True
            self.shield_start_time = time.monotonic()

            self.inventory[5] = None
            self.active_weapon = self.inventory[6]
            self.active_weapon_id = self.active_weapon.wid
            self.broadcast_weapon_update(self.active_weapon_id)
            self.send_inventory_state()



    def change_pos(self, intent):
        global LAVA_DAMAGE
        dir_x = 0
        dir_y = 0

        if intent & LEFT:
            dir_x -= 1
            self.dir = 2  # שמאלה
        if intent & RIGHT:
            dir_x += 1
            self.dir = 3  # ימינה
        if intent & UP:
            dir_y -= 1
            self.dir = 1  # למעלה
        if intent & DOWN:
            dir_y += 1
            self.dir = 0  # למטה


        if self.shoot_pending:
            self.shoot_pending = False

            now = time.monotonic()
            w = self.active_weapon


            if now - w.last_time_use > w.cooldown:
                if w.kind=="strength":
                    PISTOL.set_damage(10)
                    KNIFE.set_damage(20)
                if w.kind=="shield":
                    LAVA_DAMAGE=2.5
                w.last_time_use = 0

            w.last_time_use = now

            if now - self.last_weapon_use < w.cooldown:
                return

            self.last_weapon_use = now

            if w.kind == "ranged":
                self.fire_ranged_weapon(w)
            elif w.kind == "melee":
                self.fire_melee_weapon(w)
            elif w.kind == "spell":
                self.spell(w)



        length = math.hypot(dir_x, dir_y)
        if length != 0:
            dir_x /= length
            dir_y /= length
            self.last_dir_x = dir_x
            self.last_dir_y = dir_y

        speed = SPEED
        if intent & SPRINT and not intent & CROUCH:
            speed = SPRINT_SPEED
        elif intent & CROUCH and not intent & SPRINT:
            speed = CROUCH_SPEED

        if dir_x != 0 or dir_y != 0:
            dx = dir_x * speed * SERVER_TICK
            dy = dir_y * speed *SERVER_TICK

            if dx != 0:
                self.collisions(dx, 0)
            if dy != 0:
                self.collisions(0, dy)

    def collisions(self, dx, dy):
        tolerance = 6.0

        entities = []
        for client in list(CONNECTED_CLIENTS):
            if client is self:
                continue
            entities.append((client.x, client.y))

        for pid, ghost in list(SHADOW_PLAYERS.items()):
            if pid == self.client_id:
                continue
            entities.append((ghost["x"], ghost["y"]))

        allowed_dx = dx
        allowed_dy = dy

        # התנגשות מול שחקנים אחרים
        for ex, ey in entities:
            if abs(ex - self.x) > (PLAYER_WIDTH + abs(dx)):
                continue
            if abs(ey - self.y) > (PLAYER_HEIGHT + abs(dy)):
                continue

            inline_x = abs(self.x - ex) < (PLAYER_WIDTH - tolerance)
            inline_y = abs(self.y - ey) < (PLAYER_HEIGHT - tolerance)

            if dx != 0 and inline_y:
                if dx * (ex - self.x) > 0:
                    if dx > 0:
                        test_x = self.x + allowed_dx
                        if self.x <= ex and test_x > ex - (PLAYER_WIDTH - tolerance):
                            allowed_dx = max(
                                0.0,
                                min(allowed_dx, ex - self.x - (PLAYER_WIDTH - tolerance))
                            )
                    elif dx < 0:
                        test_x = self.x + allowed_dx
                        if self.x >= ex and test_x < ex + (PLAYER_WIDTH - tolerance):
                            allowed_dx = min(
                                0.0,
                                max(allowed_dx, ex + (PLAYER_WIDTH - tolerance) - self.x)
                            )

            if dy != 0 and inline_x:
                if dy * (ey - self.y) > 0:
                    if dy > 0:
                        test_y = self.y + allowed_dy
                        if self.y <= ey and test_y > ey - (PLAYER_HEIGHT - tolerance):
                            allowed_dy = max(
                                0.0,
                                min(allowed_dy, ey - self.y - (PLAYER_HEIGHT - tolerance))
                            )
                    elif dy < 0:
                        test_y = self.y + allowed_dy
                        if self.y >= ey and test_y < ey + (PLAYER_HEIGHT - tolerance):
                            allowed_dy = min(
                                0.0,
                                max(allowed_dy, ey + (PLAYER_HEIGHT - tolerance) - self.y)
                            )

        # התנגשות מול עצים / tiles חוסמים
        if allowed_dx != 0:
            test_x = self.x + allowed_dx
            if not player_would_collide(test_x, self.y):
                self.x = test_x

        if allowed_dy != 0:
            test_y = self.y + allowed_dy
            if not player_would_collide(self.x, test_y):
                self.y = test_y

        self.x = max(-MAP_HALF_WIDTH, min(self.x, MAP_HALF_WIDTH - PLAYER_WIDTH))
        self.y = max(-MAP_HALF_HEIGHT, min(self.y, MAP_HALF_HEIGHT - PLAYER_HEIGHT))

        self.check_chunk_boundaries()

    def check_chunk_boundaries(self):
        if time.monotonic() - getattr(self, 'joined_server_time', 0) < 1.5:
            return

        if not MY_CHUNKS: return

        chunk_x = (int(self.x) // CHUNK_WIDTH) * CHUNK_WIDTH
        chunk_y = (int(self.y) // CHUNK_HEIGHT) * CHUNK_HEIGHT

        if [chunk_x, chunk_y] not in MY_CHUNKS and self.authenticated:
            # [FIXED] Prevent spamming requests, but don't de-authenticate yet!
            if self.handoff_in_progress:
                return

            target_peer = None
            for peer_id, info in PEER_INFO.items():
                if [chunk_x, chunk_y] in info.get("chunks", []):
                    target_peer = peer_id
                    break

            if target_peer and target_peer in PEER_CONNECTIONS:
                print(f"[HANDOFF] Player {self.user_name} routing to {target_peer}...")
                self.handoff_in_progress = True
                request_handoff_from_mesh(self, target_peer)

            else:
                print(f"[MAP] Chunk {chunk_x}, {chunk_y} is unowned. Claiming it dynamically.")
                MY_CHUNKS.append([chunk_x, chunk_y])

                payload = json.dumps({
                    "type": "CHUNK_UPDATE",
                    "server_id": SERVER_SHORT_ID,
                    "chunks": MY_CHUNKS
                }).encode()
                packet = struct.pack("!I", len(payload)) + payload

                for peer_id, w in list(PEER_CONNECTIONS.items()):
                    if not w.is_closing():
                        try:
                            w.write(packet)
                        except:
                            pass

                if MANAGER_CLIENT:
                    try:
                        stream_id = MANAGER_CLIENT._quic.get_next_available_stream_id(False)
                        MANAGER_CLIENT._quic.send_stream_data(stream_id, payload + b'\n', end_stream=False)
                        MANAGER_CLIENT.transmit()
                    except: pass

    def connection_loss(self):
        if self.client_id:
            inv = self.get_inventory_save_data()

            send_to_manager({
                "type": "PLAYER_LEFT",
                "server_id": SERVER_SHORT_ID,
                "player_id": self.client_id.hex,
                "x": self.x,
                "y": self.y,
                "hp": self.hp,
                "bow": inv["bow"],
                "heal": inv["heal"],
                "strength": inv["strength"],
                "shield": inv["shield"],
                "active_weapon_id": inv["active_weapon_id"],
            })

        if self in CONNECTED_CLIENTS:
            CONNECTED_CLIENTS.remove(self)

        print(f"Client {self.client_id} disconnected")

        for client in list(CONNECTED_CLIENTS):
            payload = struct.pack("!B16s", 3, self.client_id.bytes)
            packet = struct.pack("!H", len(payload)) + payload
            try:
                client._quic.send_stream_data(client.state_stream_id, packet, end_stream=False)
            except:
                pass

    def connection_lost(self, exc):
        self.connection_loss()

    # ===========================
    # BROADCASTS
    # ===========================

    def broadcast_world_state(self):
        payload = struct.pack("!B16sfffB", 1, self.client_id.bytes, self.x, self.y, self.hp, self.dir)
        packet = struct.pack("!H", len(payload)) + payload

        # 2. Send it to EVERYONE ELSE
        for client in list(CONNECTED_CLIENTS):
            if client is not self and getattr(client, 'authenticated', False):
                try:
                    client._quic.send_stream_data(client.state_stream_id, packet, end_stream=False)
                except: pass


    def broadcast_online_clients(self):
        for client in list(CONNECTED_CLIENTS):
            payload = struct.pack(
                "!B16sfffB",
                2,                    # msg_type = online members
                client.client_id.bytes,
                client.x,
                client.y,
                client.hp,
                client.active_weapon_id
            )

            packet = struct.pack("!H", len(payload)) + payload
            self._quic.send_stream_data(self.state_stream_id, packet, end_stream=False)
            self.transmit()

    def broadcast_new_connection(self):
        payload = struct.pack(
            "!B16sfff",
            5,
            self.client_id.bytes,
            self.x,
            self.y,
            self.hp
        )
        packet = struct.pack("!H", len(payload)) + payload

        for client in list(CONNECTED_CLIENTS):
            try:
                client._quic.send_stream_data(client.state_stream_id, packet, end_stream=False)
            except: pass

    def send_self_movement(self):
        payload = struct.pack("!B16sffH", 4, self.client_id.bytes, self.x, self.y, self.last_seq)
        packet = struct.pack("!H", len(payload)) + payload
        self._quic.send_stream_data(self.control_stream_id, packet, end_stream=False)


    def send_hp_update(self):
        payload = struct.pack("!B16sfH", 7, self.client_id.bytes, self.hp, self.damage_seq)
        packet = struct.pack("!H", len(payload)) + payload
        self._quic.send_stream_data(self.control_stream_id, packet, end_stream=False)
        self.transmit()

    def broadcast_hp_update(self):
        payload = struct.pack("!B16sfH", 8, self.client_id.bytes, self.hp, self.damage_seq)
        packet = struct.pack("!H", len(payload)) + payload

        for client in list(CONNECTED_CLIENTS):
            if client is self:
                continue

            try:
                client._quic.send_stream_data(client.control_stream_id, packet, end_stream=False)
            except: pass

    def broadcast_message(self, message):
        user_bytes = self.user_name.encode()
        payload = (struct.pack(
            "!BII",
            9,
            len(user_bytes),
            len(message),
        )
        + user_bytes
        + message
        )

        packet = struct.pack("!H", len(payload)) + payload

        for client in list(CONNECTED_CLIENTS):
            if client is self:
                continue

            try:
                client._quic.send_stream_data(client.control_stream_id, packet, end_stream=False)
            except: pass

    def respawn(self):
        self.x = -PLAYER_WIDTH // 2
        self.y = -PLAYER_HEIGHT // 2
        self.hp = 100

        # important: new authoritative event
        self.damage_seq = (self.damage_seq + 1) & 0xFFFF

        # send BOTH hp + position
        self.send_hp_update()
        self.send_self_movement()
        self.broadcast_world_state()


# ===========================
# ONE TIME FUNCTION
# ===========================

def spawn_enemies():
    for i in range(25):
        e = Enemy(i)
        e.respawn_enemy()
        ENEMIES.append(e)

enemy_index = 0

async def enemy_loop():
    global enemy_index
    tick_count = 0
    batch_size = 10

    while True:
        if ENEMIES:
            for _ in range(batch_size):
                e = ENEMIES[enemy_index]
                e.update()
                enemy_index = (enemy_index + 1) % len(ENEMIES)

        for bullet in ENEMY_BULLETS[:]:
            bullet.update()
            if not bullet.alive:
                bullet.broadcast_despawn()
                ENEMY_BULLETS.remove(bullet)

        if tick_count % 3 == 0:

            for e in ENEMIES:
                e.broadcast_enemy()

        tick_count += 1
        await asyncio.sleep(SERVER_TICK)



def seq_newer(a, b):
    return ((a - b) & (SEQ_MAX - 1)) < SEQ_HALF


async def load_tile_map(path: str):
    tile_dict = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for ty, line in enumerate(f):
                for tx, ch in enumerate(line.strip("\n")):
                    if ch not in TILE_DEFS: continue
                    walkable = TILE_DEFS[ch]
                    tile_dict[(tx, ty)] = walkable
    except Exception:
        print(f"[MAP] Warning: Could not find {path}, loading empty map.")
    return tile_dict


def player_would_collide(x, y):
    col_left   = x
    col_right  = x + PLAYER_WIDTH
    col_top    = y
    col_bottom = y + PLAYER_HEIGHT

    left_tile   = int((col_left   + MAP_HALF_WIDTH)  // TILE_SIZE)
    right_tile  = int((col_right  + MAP_HALF_WIDTH)  // TILE_SIZE)
    top_tile    = int((col_top    + MAP_HALF_HEIGHT) // TILE_SIZE)
    bottom_tile = int((col_bottom + MAP_HALF_HEIGHT) // TILE_SIZE)

    for tx in range(left_tile, right_tile + 1):
        for ty in range(top_tile, bottom_tile + 1):
            tile = TILE_DICT.get((tx, ty))
            if tile and not tile['walkable']:
                return True
    return False

# ===========================
# BACKGROUND TASKS
# ===========================
async def manager_connector_loop():
    while True:
        try:
            await connect_to_manager()
        except Exception as e:
            print(f"[MANAGER] Connection lost: {e}")

        # wait before retrying
        await asyncio.sleep(5)

async def bullets_tick():
    while True:
        await asyncio.sleep(SERVER_TICK)

        for p in list(CONNECTED_CLIENTS):
            if not getattr(p, "bullet_active", False):
                continue

            p.bullet_x += p.bullet_vx * SERVER_TICK
            p.bullet_y += p.bullet_vy * SERVER_TICK
            p.bullet_ttl -= SERVER_TICK

            # TODO בהמשך: collision בדיקה כאן (tilemap, players וכו)
            # hit = check_bullet_collision(p.bullet_x, p.bullet_y)
            # if hit: ... despawn + hit event ...
            for enemy in list(ENEMIES):
                if abs(p.bullet_x - enemy.x) < 50 and abs(p.bullet_y - enemy.y) < 50:
                    enemy.hp -= p.get_weapon_damage(p.active_weapon)
                    p.bullet_active = False
                    p.broadcast_bullet_despawn()

                    if enemy.hp <= 0:
                        enemy.hp = 0
                        p.give_random_enemy_drop()
                        p.give_random_enemy_drop()
                        enemy.respawn_enemy()
                    else:
                        enemy.broadcast_enemy()

                    break


            if p.bullet_ttl <= 0:
                p.bullet_active = False
                p.broadcast_bullet_despawn()


async def server_movement_tick():
    global CPU_USAGE, shield_active, shield_duration, shield_start_time, strength_start_time, strength_active, shield_duration, LAVA_DAMAGE
    accumulator = 0.0
    last_time = time.perf_counter()
    sync_tick = 0
    while True:
        current_time = time.perf_counter()
        frame_time = current_time - last_time
        last_time = current_time

        # Cap frame time to avoid the "Spiral of Death" if the server hangs
        if frame_time > 0.25:
            frame_time = 0.25

        accumulator += frame_time

        # --- THE FIXED TIMESTEP ACCUMULATOR ---
        while accumulator >= SERVER_TICK:
            for client in list(CONNECTED_CLIENTS):
                needs_broadcast = False
                current = getattr(client, 'current_intent', 0)

                if client.shield_active:
                    if time.monotonic() - client.shield_start_time > client.shield_duration:
                        client.shield_active = False

                if client.strength_active:
                    if time.monotonic() - client.strength_start_time > client.strength_duration:
                        client.strength_active = False

                # BOT MOVEMENT LOGIC
                if getattr(client, 'bot_mode', False):
                    now = time.monotonic()

                    if client.bot_steps <= 0:
                        if now >= client.bot_next_move_time:
                            client.pick_new_bot_move()
                            client.bot_next_move_time = now + 0.8
                    else:
                        # Translate the xorshift direction into your actual movement flags!
                        bot_intent = 0
                        if client.bot_direction == 0:
                            bot_intent = UP  # 1 << 0
                        elif client.bot_direction == 1:
                            bot_intent = RIGHT  # 1 << 3
                        elif client.bot_direction == 2:
                            bot_intent = DOWN  # 1 << 2
                        elif client.bot_direction == 3:
                            bot_intent = LEFT  # 1 << 1

                        client.current_intent = bot_intent
                        # Use your built-in movement so the bot respects collisions!
                        client.change_pos(bot_intent)
                        client.bot_steps -= 1

                        needs_broadcast = True
                        client.was_moving = True

                        if sync_tick % 5 == 0:
                            client.send_self_movement()

                # NORMAL PLAYER MOVEMENT
                else:
                    is_moving = bool(current & (UP | LEFT | DOWN | RIGHT))
                    should_process = is_moving or client.shoot_pending

                    if should_process:
                        client.change_pos(current)

                        if is_moving:
                            client.last_seq = (getattr(client, 'last_seq', 0) + 1) % 65536
                            client.was_moving = True
                        else:
                            client.was_moving = False

                        needs_broadcast = True

                        if sync_tick % 5 == 0:
                            client.send_self_movement()

                    elif getattr(client, 'was_moving', False):
                        needs_broadcast = True
                        client.was_moving = False
                        client.send_self_movement()

                # Broadcast to everyone if they moved (bot or normal)
                if needs_broadcast:
                    client.broadcast_world_state()

            for client in list(CONNECTED_CLIENTS):
                if getattr(client, 'authenticated', False):
                    try:
                        client.transmit()
                    except:
                        pass

            accumulator -= SERVER_TICK
            sync_tick += 1

            if sync_tick >= 60:
                CPU_USAGE = CURRENT_PROCESS.cpu_percent(interval=None)
                sync_tick = 0

        # Yield control back to asyncio
        await asyncio.sleep(0.001)


async def check_tile():
    while True:
        await asyncio.sleep(LAVA_INTERVAL)

        for client in list(CONNECTED_CLIENTS):
            if not getattr(client, 'authenticated', False): continue

            tx = int((client.x + (PLAYER_WIDTH - 18) + MAP_HALF_WIDTH) // TILE_SIZE)
            ty = int((client.y + (PLAYER_HEIGHT - 8) + MAP_HALF_HEIGHT) // TILE_SIZE)

            tile = TILE_DICT.get((tx, ty))

            if tile and tile['damages']:
                if client.shield_active:
                    continue

                client.damage_seq = (client.damage_seq + 1) & 0xFFFF
                client.hp -= LAVA_DAMAGE

                if client.hp <= 0:
                    client.respawn()
                    continue

                client.send_hp_update()
                client.broadcast_hp_update()


async def check_heartbeats():
    """Background task to detect dead connections"""
    while True:
        await asyncio.sleep(2)
        current_time = time.time()

        for client in list(CONNECTED_CLIENTS):
            if current_time - client.last_heartbeat > client.heartbeat_timeout:
                print(f"Client {client.client_id} timed out (no heartbeat)")
                client.connection_loss()
                try:
                    client._quic.close()
                except:
                    print("cant close connection")
                    pass


async def start_server():
    # Quic settings
    config = QuicConfiguration(
        is_client=False,  # This is not a client this is a server.
        alpn_protocols=["mmo"]  # ALPN = Aplication Layer Protocol Negotiation.
        # This means after encryption starts, it asks what kind of protocol are you using?
        # And I say mmo (its like a handshake label, there is no such protocol as mmo).
    )
    config.load_cert_chain(certfile="server.cert.pem", keyfile="server.key.pem")
    # The certificate contains my public key and the server identity info.
    # The certificate proves who you are and the private key proves you own it.

    create_bg_task(check_heartbeats())
    create_bg_task(server_movement_tick())
    create_bg_task(check_tile())
    create_bg_task(bullets_tick())
    create_bg_task(enemy_loop())

    # Mesh Background Tasks
    create_bg_task(start_backend_mesh())
    create_bg_task(server_to_server_sync())
    create_bg_task(cleanup_shadows())

    await serve(  # Pause the whole function until this is done (until server is fully started)
        "0.0.0.0",  # Anyone wanting to connect can connect
        4433,  # The server is on port 4433
        configuration=config,  # Set the configuration (rules of the connection)
        create_protocol=GameServerProtocol  # For each client connection, create a new GameServerProtocol objet
    )

    try:
        await asyncio.Future()  # Run this forever
    except asyncio.CancelledError:
        print()


async def main():
    global TILE_DICT

    TILE_DICT = await load_tile_map(MAP_PATH)

    server_task = asyncio.create_task(start_server())
    manager_connect = asyncio.create_task(manager_connector_loop())
    load_balance = asyncio.create_task(load_balancer_loop())

    try:
        await asyncio.gather(server_task, manager_connect, load_balance)
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    asyncio.run(main())